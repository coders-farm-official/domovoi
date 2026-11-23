"""scripts/linux/uninstall.sh: keep data (the default) vs remove everything.

Each case runs the real script under bash (Git for Windows' bash on
Windows, as test_linux_install_script.py does) in its test harness mode: a
throwaway root folder shaped like an installed server (the service user's
home with its server identity, the program folder, unit files, sudo grants,
records), stubs for systemctl and docker that record what they were asked,
and an answers file standing in for /dev/tty. Nothing on this machine is
touched.

What they hold the script to (S3-4, owner decision 2026-11-22): keep data
is the default and keeps the server's identity, the database and the
settings where they are; remove everything deletes them only when it was
asked for explicitly (typed, or --yes); with no terminal and no answer it
changes nothing; and both say plainly what is kept or deleted, and where.
"""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

import pytest

from domovoi.tests.test_apply_update_script import BASH, requires_bash

REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPT = REPO_ROOT / "scripts" / "linux" / "uninstall.sh"
FP = "SHA256:AbCdEfGhIjKlMnOpQrStUvWxYz0123456789abcdEFG"

SYSTEMCTL = """#!/usr/bin/env bash
printf 'systemctl %s\\n' "$*" >>"$STUB_LOG"
case $1 in cat) exit 1 ;; esac
exit 0
"""

DOCKER = """#!/usr/bin/env bash
printf 'docker %s\\n' "$*" >>"$STUB_LOG"
case "$1 $2" in
  "ps -a") printf 'domovoi-postgres\\ndomovoi-mpd-kitchen\\nsomeone-else\\n' ;;
  "volume ls") printf 'domovoi_domovoi-pgdata\\nunrelated-volume\\n' ;;
esac
exit 0
"""


def test_script_is_lf_only():
    assert b"\r\n" not in SCRIPT.read_bytes()


@requires_bash
def test_script_parses():
    proc = subprocess.run([BASH, "-n", str(SCRIPT)], capture_output=True, text=True)
    assert proc.returncode == 0, proc.stderr


class Box:
    def __init__(self, tmp: Path):
        self.tmp = tmp
        self.root = tmp / "root"
        r = self.root
        (r / "etc").mkdir(parents=True)
        (r / "etc" / "passwd").write_text(
            "root:x:0:0:root:/root:/bin/bash\ndomovoi:x:999:999::/home/domovoi:/bin/bash\n",
            encoding="utf-8")
        self.identity = r / "home" / "domovoi" / ".domovoi" / "server-identity.json"
        self.identity.parent.mkdir(parents=True)
        self.identity.write_text(json.dumps({
            "schema": 1, "algorithm": "ed25519", "private_key": "c2VjcmV0",
            "public_key": "cHVibGlj", "fingerprint": FP,
        }, indent=2), encoding="utf-8")
        self.checkout = r / "opt" / "domovoi"
        (self.checkout / "scripts" / "linux").mkdir(parents=True)
        (self.checkout / "scripts" / "linux" / "provision.sh").write_text("#!/bin/sh\n", encoding="utf-8")
        (self.checkout / "domovoi").mkdir()
        (self.checkout / "domovoi" / ".env").write_text("POSTGRES_PASSWORD=x\n", encoding="utf-8")
        units = r / "etc" / "systemd" / "system"
        units.mkdir(parents=True)
        for u in ("domovoi-core.service", "domovoi-web.service", "domovoi-db.service",
                  "domovoi-update.service", "domovoi-cache-trim.timer", "ssh.service"):
            (units / u).write_text("[Unit]\n", encoding="utf-8")
        (r / "etc" / "sudoers.d").mkdir()
        for g in ("domovoi-restart", "domovoi-update", "alex"):
            (r / "etc" / "sudoers.d" / g).write_text("#\n", encoding="utf-8")
        (r / "var" / "lib" / "domovoi-provision").mkdir(parents=True)
        (r / "var" / "lib" / "domovoi-provision" / "result.json").write_text("{}", encoding="utf-8")
        (r / "srv" / "media" / "music").mkdir(parents=True)
        (r / "srv" / "media" / "music" / "song.flac").write_bytes(b"x")
        self.bin = tmp / "bin"
        self.bin.mkdir()
        for name, body in (("systemctl", SYSTEMCTL), ("docker", DOCKER)):
            p = self.bin / name
            p.write_bytes(body.encode("utf-8"))
            p.chmod(0o755)
        self.log = tmp / "stub.log"

    def run(self, *args: str, answers: str | None = None) -> subprocess.CompletedProcess:
        tty = "none"
        if answers is not None:
            f = self.tmp / "answers"
            f.write_text(answers, encoding="utf-8")
            tty = f.as_posix()
        self.log.unlink(missing_ok=True)
        # Git Bash: the stubs' folder first on bash's own PATH.
        prefix = f'export PATH="$(cygpath -u "{self.bin.as_posix()}" 2>/dev/null || echo "{self.bin.as_posix()}"):$PATH"; '
        env = {
            "DOMOVOI_UNINSTALL_TEST_HARNESS": "1",
            "DOMOVOI_UNINSTALL_ROOT": self.root.as_posix(),
            "DOMOVOI_UNINSTALL_TTY": tty,
            "STUB_LOG": self.log.as_posix(),
        }
        full_env = {**os.environ, **env}
        cmd = prefix + 'exec bash "$0" "$@"'
        return subprocess.run(
            [BASH, "-c", cmd, SCRIPT.as_posix(), *args],
            capture_output=True, text=True, env=full_env, timeout=120, stdin=subprocess.DEVNULL,
        )

    @property
    def calls(self) -> str:
        return self.log.read_text(encoding="utf-8") if self.log.exists() else ""


@pytest.fixture
def box(tmp_path: Path) -> Box:
    return Box(tmp_path)


def _integration_gone(box: Box) -> None:
    r = box.root
    assert not (r / "etc" / "systemd" / "system" / "domovoi-core.service").exists()
    assert not (r / "etc" / "systemd" / "system" / "domovoi-cache-trim.timer").exists()
    assert not (r / "etc" / "sudoers.d" / "domovoi-restart").exists()
    assert not (r / "etc" / "sudoers.d" / "domovoi-update").exists()
    # Not Domovoi's: never touched.
    assert (r / "etc" / "systemd" / "system" / "ssh.service").exists()
    assert (r / "etc" / "sudoers.d" / "alex").exists()
    assert (r / "srv" / "media" / "music" / "song.flac").exists()


@requires_bash
def test_keep_data_is_the_default_answer_and_keeps_the_identity(box: Box):
    """At a terminal, Enter picks keep data, Enter confirms; the identity,
    the settings, the program folder and the database stay; the system
    wiring goes."""
    proc = box.run(answers="\n\n")
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "1) Keep my data (recommended)" in proc.stderr
    assert box.identity.exists()
    assert (box.checkout / "domovoi" / ".env").exists()
    assert (box.root / "var" / "lib" / "domovoi-provision" / "result.json").exists()
    _integration_gone(box)
    calls = box.calls
    assert "systemctl disable --now domovoi-core.service" in calls
    assert "docker stop domovoi-postgres" in calls
    assert "docker stop domovoi-mpd-kitchen" in calls
    assert "someone-else" not in calls.replace("ps -a", "")
    assert "volume rm" not in calls and "docker rm" not in calls
    # Says plainly what is kept, and where.
    out = proc.stdout
    assert f"the server's identity ({FP}): /home/domovoi/.domovoi/server-identity.json" in out
    assert "domovoi_domovoi-pgdata" in out
    assert "data was kept" in out
    # The key itself never reaches the output.
    assert "c2VjcmV0" not in out + proc.stderr


@requires_bash
def test_keep_data_unattended(box: Box):
    proc = box.run("--keep-data", "--yes")
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert box.identity.exists()
    _integration_gone(box)


@requires_bash
def test_remove_everything_needs_the_phrase_typed(box: Box):
    proc = box.run("--remove-everything", answers="yes\n")
    assert proc.returncode != 0
    assert "nothing was changed" in proc.stderr
    assert box.identity.exists()
    assert (box.root / "etc" / "sudoers.d" / "domovoi-restart").exists()
    assert "disable" not in box.calls


@requires_bash
def test_remove_everything_from_the_menu(box: Box):
    proc = box.run(answers="2\nremove everything\n")
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert not box.identity.exists()
    assert not box.identity.parent.exists()
    assert not box.checkout.exists()
    assert not (box.root / "var" / "lib" / "domovoi-provision").exists()
    _integration_gone(box)
    calls = box.calls
    assert "docker volume rm domovoi_domovoi-pgdata" in calls
    assert "unrelated-volume" not in calls.replace("volume ls", "")
    assert "docker rm -f domovoi-postgres" in calls
    # Warned before: the rooms must be set up again, and how to keep it instead.
    assert "must be set up again" in proc.stdout
    assert "House identity backup" in proc.stdout


@requires_bash
def test_remove_everything_unattended_needs_yes(box: Box):
    proc = box.run("--remove-everything")
    assert proc.returncode == 2
    assert box.identity.exists()
    proc = box.run("--remove-everything", "--yes")
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert not box.identity.exists()


@requires_bash
def test_no_terminal_and_no_answer_changes_nothing(box: Box):
    proc = box.run()
    assert proc.returncode == 2
    assert box.identity.exists()
    assert (box.root / "etc" / "sudoers.d" / "domovoi-restart").exists()
    assert box.calls.count("disable") == 0


@requires_bash
def test_dry_run_changes_nothing(box: Box):
    proc = box.run("--remove-everything", "--dry-run")
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "nothing was changed" in proc.stdout
    assert box.identity.exists()
    assert box.checkout.exists()
    assert "docker rm" not in box.calls and "volume rm" not in box.calls
    assert "disable" not in box.calls


@requires_bash
def test_a_folder_that_is_not_a_checkout_is_not_deleted(box: Box):
    (box.checkout / "scripts" / "linux" / "provision.sh").unlink()
    proc = box.run("--remove-everything", "--yes")
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert box.checkout.exists()
    assert "doesn't look like Domovoi's program folder" in proc.stderr


@requires_bash
def test_the_test_variables_are_refused_outside_the_harness(box: Box):
    env = {**os.environ, "DOMOVOI_UNINSTALL_ROOT": box.root.as_posix()}
    proc = subprocess.run([BASH, SCRIPT.as_posix(), "--dry-run"], capture_output=True, text=True,
                          env=env, timeout=60, stdin=subprocess.DEVNULL)
    assert proc.returncode == 1
    assert "for this script's tests only" in proc.stderr
