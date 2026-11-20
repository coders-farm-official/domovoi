"""New card files written onto a card that was set up before (S3-6).

Stage 1 keeps its step markers on the ROOT partition
(/var/lib/domovoi/bootstrap-steps); media prep writes the BOOT partition.
Re-writing a used card's files left the old root's markers in place, so
every step skipped - including the cmdline cleanup - and the
``systemd.run`` hook stayed in cmdline.txt: every boot re-entered stage 1,
exited 0 in seconds and rebooted. Slow amber forever, no setup Wi-Fi.
Found on real hardware, 2026-11.

These run the WHOLE rendered stage 1 under bash, with its absolute paths
moved under a temporary root and the commands that need a real Pi (user
accounts, systemd, dpkg, the venv) replaced by small shell functions. What
is real: the step markers, the card stamp, the payload checksum and
extract, the file copies, and the cmdline cleanup through boot_put.

No DB, no hardware; skips without a bash.
"""

from __future__ import annotations

import hashlib
import io
import json
import os
import re
import shutil
import subprocess
import sys
import tarfile
from pathlib import Path

import pytest

from domovoi.satellite_media import overlay

USER = "domo"
HOOK = (
    "systemd.run=/boot/firmware/domovoi/firstrun.sh "
    "systemd.run_success_action=reboot systemd.unit=kernel-command-line.target"
)
BASE_CMDLINE = "console=tty1 root=PARTUUID=abcd-02 rootfstype=ext4 rootwait modules-load=dwc2"

# Root-partition paths stage 1 uses, moved under the test's root. Payload
# sub-paths ("$PAYDIR"/debs) and /dev/null are not in the list.
_ABS = re.compile(r"(?<![\w.$}/])/(var/lib|var/log|opt|etc|usr/local|boot|tmp)/")

# Stand-ins for what only a real Pi has. Each is a function, which beats
# PATH; everything else (tar, sha256sum, cp, boot_put's mv) is real.
_STUBS = r'''
# passwd fields are colon-separated: the home must be a path without a
# drive letter's colon, which is what Git Bash's pwd gives.
getent() { printf '%s:x:1000:1000::%s:/bin/bash\n' "$2" "$(cd "$FAKE_ROOT/home" && pwd)/$2"; }
id() { [ -d "$FAKE_ROOT/home/$1" ]; }
useradd() { mkdir -p "$FAKE_ROOT/home/${@: -1}"; }
usermod() { :; }
gpasswd() { :; }
chown() { :; }
systemctl() { echo "systemctl $*" >>"$FAKE_ROOT/systemctl.log"; }
rfkill() { :; }
iw() { :; }
setupcon() { :; }
dpkg-deb() { return 1; }
install() {
  local a=()
  while [ $# -gt 0 ]; do
    case $1 in -m|-o|-g) shift 2 ;; -*) shift ;; *) a+=("$1"); shift ;; esac
  done
  cp "${a[0]}" "${a[1]}"
}
python3() {
  if [ "${1:-}" = "-m" ] && [ "${2:-}" = "venv" ]; then
    mkdir -p "${@: -1}/bin"; echo built >"${@: -1}/bin/built"; return 0
  fi
  "$REAL_PY" "$@"
}
'''


def _bash() -> str:
    bash = shutil.which("bash")
    if bash is None:
        pytest.skip("no bash available to run stage 1")
    return bash


def _payload(version: str) -> bytes:
    """A small payload.tar.gz shaped like the real one (one top directory,
    stripped on extract)."""
    files = {
        "payload/satellite/__init__.py": "",
        "payload/satellite/VERSION": version,
        "payload/satellite/requirements.txt": "numpy>=1.26\n",
        "payload/pyproject.toml": "[project]\nname = 'x'\n",
        "payload/plugins/radio/manifest.json": "{}",
    }
    if version == "1":
        # A module the next payload drops.
        files["payload/satellite/old_only.py"] = ""
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tar:
        for name, text in files.items():
            data = text.encode()
            info = tarfile.TarInfo(name)
            info.size = len(data)
            tar.addfile(info, io.BytesIO(data))
    return buf.getvalue()


class Card:
    """A temporary root + boot partition, and a way to (re)write card files
    onto it the way media prep does."""

    def __init__(self, root: Path):
        self.root = root
        self.boot = root / "boot" / "firmware"
        (self.boot / "domovoi").mkdir(parents=True)
        self.state = root / "var" / "lib" / "domovoi"
        self.steps = self.state / "bootstrap-steps"
        self.home = root / "home" / USER
        # What a Pi OS root already has.
        for d in ("home", "etc/default", "etc/sudoers.d", "etc/systemd/system",
                  "usr/local/sbin", "var/log", "tmp"):
            (root / d).mkdir(parents=True, exist_ok=True)

    def write_card(self, version: str, prep_epoch: float) -> None:
        tar = _payload(version)
        d = self.boot / "domovoi"
        (d / "payload.tar.gz").write_bytes(tar)
        (d / "payload.sha256").write_text(
            f"{hashlib.sha256(tar).hexdigest()}  payload.tar.gz\n", newline="\n")
        (d / "build-info.json").write_text(json.dumps({"version": version}))
        (d / "device-info.json").write_text(json.dumps(
            overlay.initial_device_info("voice", setup_transport="portal")))
        (d / "ap.json").write_text(json.dumps({"ssid": f"Domovoi-Setup-{version}"}))
        body = overlay.render_firstrun(USER, "xvf3800_usb", "voice", "portal",
                                       prep_epoch=prep_epoch)
        body = _ABS.sub(lambda m: f'{self.root.as_posix()}/{m.group(1)}/', body)
        (d / "firstrun.sh").write_text(body, encoding="utf-8", newline="\n")
        self.hook()

    def hook(self) -> None:
        """The cmdline media prep leaves: the stock line plus our hook."""
        (self.boot / "cmdline.txt").write_text(
            f"{BASE_CMDLINE} {HOOK}\n", encoding="utf-8", newline="\n")

    def boot_once(self) -> subprocess.CompletedProcess:
        """One boot: the kernel hook runs the script by its path on the card,
        so $0 (and with it BOOT) is the real thing; the stubs come in
        through BASH_ENV."""
        stubs = self.root / "stubs.sh"
        stubs.write_text(_STUBS, encoding="utf-8", newline="\n")
        env = {**os.environ, "BASH_ENV": stubs.as_posix(),
               "FAKE_ROOT": self.root.as_posix(),
               "REAL_PY": Path(sys.executable).as_posix()}
        script = self.boot / "domovoi" / "firstrun.sh"
        proc = subprocess.run([_bash(), script.as_posix()], capture_output=True,
                              text=True, encoding="utf-8", env=env, cwd=self.root)
        assert proc.returncode == 0, proc.stdout + proc.stderr + self.log()
        return proc

    def log(self) -> str:
        p = self.root / "var" / "log" / "domovoi-firstrun.log"
        return p.read_text(encoding="utf-8", errors="replace") if p.is_file() else ""

    def cmdline(self) -> str:
        return (self.boot / "cmdline.txt").read_text(encoding="utf-8")

    def code(self) -> Path:
        return self.home / "domovoi" / "satellite"


@pytest.fixture
def card(tmp_path):
    _bash()
    c = Card(tmp_path / "card")
    c.write_card("1", prep_epoch=1_700_000_000)
    return c


def _set_up_and_adopted(card: Card) -> None:
    """Stage 1 finished, the unit adopted and stage 2 done - a used card."""
    card.boot_once()
    assert card.cmdline() == BASE_CMDLINE + "\n"
    assert (card.steps / "cmdline").is_file()
    (card.home / ".domovoi" / "config.toml").write_text("[satellite]\nroom = 'kitchen'\n")
    (card.home / ".domovoi" / "pairing_token").write_text("old-token")
    (card.steps / "online-deps").touch()           # a stage 2 marker
    (card.state / "bootstrap-done").touch()


# ─── the loop itself ─────────────────────────────────────────────────────


def test_a_reused_card_with_stale_markers_loses_its_hook(card):
    """The hardware case: new card files on a used root. Whatever else
    happens, the hook must not survive the boot."""
    _set_up_and_adopted(card)
    card.write_card("2", prep_epoch=1_700_100_000)
    assert "systemd.run=" in card.cmdline()

    card.boot_once()

    assert card.cmdline() == BASE_CMDLINE + "\n"


def test_the_hook_goes_even_when_the_markers_say_it_went(card):
    """Step 8 reads the file. A cmdline marker beside a hook still in
    cmdline.txt is a marker from some other card, never proof."""
    card.boot_once()
    sentinel = card.code() / "LOCAL"
    sentinel.write_text("kept")
    card.hook()                                    # the hook is back...
    assert (card.steps / "cmdline").is_file()       # ...and the marker says done

    card.boot_once()

    assert card.cmdline() == BASE_CMDLINE + "\n"
    assert sentinel.read_text() == "kept"          # same card: nothing redone


# ─── a new card is a new setup ───────────────────────────────────────────


def test_new_card_files_run_every_step_again(card):
    _set_up_and_adopted(card)
    (card.home / "satellite-venv" / "bin" / "built").unlink()
    card.write_card("2", prep_epoch=1_700_100_000)

    card.boot_once()

    # The new payload is what is installed, and nothing of the old one.
    assert (card.code() / "VERSION").read_text() == "2"
    assert not (card.code() / "old_only.py").exists()
    assert (card.root / "opt" / "domovoi-payload" / "satellite" / "VERSION").read_text() == "2"
    assert not (card.root / "opt" / "domovoi-payload" / "satellite" / "old_only.py").exists()
    assert (card.home / "satellite-venv" / "bin" / "built").is_file()   # venv rebuilt
    # Plugin payloads replaced, not nested inside the old copy.
    assert (card.home / ".domovoi" / "plugin_payloads" / "radio" / "manifest.json").is_file()
    assert not (card.home / ".domovoi" / "plugin_payloads" / "plugins").exists()
    # The new card's setup network.
    ap = json.loads((card.home / ".domovoi" / "ap.json").read_text())
    assert ap["ssid"] == "Domovoi-Setup-2"
    # Stage 2 runs again for the new card.
    assert not (card.state / "bootstrap-done").exists()
    assert not (card.steps / "online-deps").exists()
    # The old adoption is kept, but out of the way, so setup starts.
    assert not (card.home / ".domovoi" / "config.toml").exists()
    assert not (card.home / ".domovoi" / "pairing_token").exists()
    kept = list((card.home / ".domovoi").glob("before-new-card-*"))
    assert len(kept) == 1
    assert (kept[0] / "config.toml").read_text().startswith("[satellite]")
    assert (kept[0] / "pairing_token").read_text() == "old-token"
    assert "systemctl disable domovoi-satellite.service" in (
        card.root / "systemctl.log").read_text()
    assert card.cmdline() == BASE_CMDLINE + "\n"


def test_a_root_from_before_the_stamp_counts_as_a_different_card(card):
    """Markers with no stamp beside them were written by an older stage 1:
    the card on the hardware bench was exactly that."""
    _set_up_and_adopted(card)
    (card.state / "bootstrap-card").unlink(missing_ok=True)
    card.write_card("2", prep_epoch=1_700_100_000)

    card.boot_once()

    assert (card.code() / "VERSION").read_text() == "2"
    assert not (card.state / "bootstrap-done").exists()


# ─── the same card resumes, fast ─────────────────────────────────────────


def test_the_same_card_after_a_power_cut_skips_what_is_done(card):
    """A re-boot of the SAME card files (a power cut before step 8) must not
    start over: the markers are trusted, the work stays, the hook goes."""
    card.boot_once()
    sentinel = card.code() / "LOCAL"
    sentinel.write_text("kept")
    (card.home / "satellite-venv" / "bin" / "built").unlink()
    (card.steps / "cmdline").unlink()
    card.hook()

    card.boot_once()

    assert sentinel.read_text() == "kept"                          # step 5 skipped
    assert not (card.home / "satellite-venv" / "bin" / "built").exists()   # step 4 skipped
    assert "starting setup over" not in card.log()
    assert card.cmdline() == BASE_CMDLINE + "\n"


def test_a_fresh_card_does_not_start_over(card):
    card.boot_once()
    assert "starting setup over" not in card.log()
    assert (card.state / "bootstrap-card").read_text().strip()
    assert not list((card.home / ".domovoi").glob("before-new-card-*"))
