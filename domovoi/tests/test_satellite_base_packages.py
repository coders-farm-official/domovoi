"""mpg123 never reached the office satellite (2026-10-17): the card side.

Debian's mpg123 needs libraries Pi OS Lite lacks, so stage 1's offline
`dpkg -i` leaves it unpacked (no /usr/bin/mpg123: that is an alternatives
link configuring creates); stage 2's `apt-get -f install -y`, run with the
Wi-Fi down and no fresh package lists, then REMOVED it; and stage 2 marked
itself done anyway. Pinned here (the satellite's own repair is in
satellite/tests/test_base_packages.py):

* the build looks for every base package in the deb cache, refetches when
  one is missing (a cache with any deb used to count as ready), and warns
  loudly when it is still missing, keeping the partial-fetch message the
  builder used to drop;
* stage 2 never runs apt's repair without fresh lists, never lets apt
  remove anything, and says the boot step retries;
* the satellite's own list of base packages is the build's.
"""

from __future__ import annotations

import asyncio
import re
from pathlib import Path

import pytest

from domovoi.satellite_media import builder, cache, fetchers, overlay, payload

REPO = Path(__file__).resolve().parents[2]


def _debs(folder: Path, *names: str) -> None:
    folder.mkdir(parents=True, exist_ok=True)
    for n in names:
        (folder / f"{n}_1.0-1_arm64.deb").write_bytes(b"!<arch>")


def test_a_base_package_missing_from_the_cache_is_named(tmp_path):
    folder = tmp_path / "debs"
    _debs(folder, *[p for p in fetchers.BASE_APT_PACKAGES if p not in ("mpg123", "libasound2")],
          "libasound2t64")
    assert fetchers.base_debs_missing("trixie", root=folder) == ["mpg123"]
    _debs(folder, "mpg123")
    assert fetchers.base_debs_missing("trixie", root=folder) == []


def test_an_empty_or_absent_cache_misses_everything(tmp_path):
    assert fetchers.base_debs_missing("trixie", root=tmp_path / "nope") == list(fetchers.BASE_APT_PACKAGES)


def test_the_warning_is_loud_and_says_what_it_costs():
    text = fetchers.base_debs_warning(["mpg123"])
    assert text.startswith("BASE PACKAGES NOT ON THIS CARD")
    assert "mpg123" in text and "online" in text and "every boot" in text and "Docker" in text


def _build(monkeypatch, tmp_path, *, fetch_result, missing_after):
    ok_status = {k: {"ok": True} for k in ("wheels", "debs", "xvf_host", "oww_models", "dtbo")}
    monkeypatch.setattr(cache, "status", lambda *a, **k: ok_status)
    monkeypatch.setattr(fetchers, "verify_oww_models", lambda dest=None: (len(fetchers.OWW_MODEL_FILES), []))
    monkeypatch.setattr(fetchers, "missing_oww_models", lambda dest=None: [])
    calls: list[str] = []
    checks = iter([["mpg123"], missing_after])
    monkeypatch.setattr(fetchers, "base_debs_missing", lambda *a, **k: next(checks))

    def _fetch(os_release, extra=(), run=None):
        calls.append(os_release)
        return fetch_result

    monkeypatch.setattr(fetchers, "fetch_debs", _fetch)

    async def _no_plugins():
        return []

    monkeypatch.setattr(payload, "enabled_satellite_plugins", _no_plugins)
    seen: dict = {}

    class Stop(Exception):
        pass

    async def _assemble(*a, **k):
        raise Stop

    monkeypatch.setattr(payload, "assemble", _assemble)
    monkeypatch.setattr(builder, "BUILDS_ROOT", tmp_path / "builds")
    # The warnings list is local to build(); catch what reaches the log.
    import logging

    records: list[str] = []

    class H(logging.Handler):
        def emit(self, record):
            records.append(record.getMessage())

    h = H()
    logging.getLogger("domovoi.satellite_media.builder").addHandler(h)
    try:
        with pytest.raises(Stop):
            asyncio.run(builder.build(
                board_id="pi02w", mic_profile="xvf3800_usb", target_kind="zip",
                target_mount=None, job_id="debs", offline=True,
            ))
    finally:
        logging.getLogger("domovoi.satellite_media.builder").removeHandler(h)
    seen["calls"] = calls
    seen["log"] = records
    return seen


def test_a_cache_without_mpg123_is_fetched_again(monkeypatch, tmp_path):
    seen = _build(monkeypatch, tmp_path, fetch_result=(True, "debs cached"), missing_after=[])
    assert seen["calls"] == ["trixie"], "a cache with some debs is no longer 'ready'"
    assert not any("BASE PACKAGES" in m for m in seen["log"])


def test_still_missing_after_the_fetch_is_said_loudly(monkeypatch, tmp_path):
    seen = _build(monkeypatch, tmp_path,
                  fetch_result=(True, "debs cached in x — could not fetch mpg123; ..."),
                  missing_after=["mpg123"])
    assert any(m.startswith("satellite media: BASE PACKAGES NOT ON THIS CARD") and "mpg123" in m
               for m in seen["log"])


def test_the_builder_keeps_the_partial_fetch_message():
    src = (REPO / "domovoi" / "satellite_media" / "builder.py").read_text(encoding="utf-8")
    assert 'if not ok or "could not fetch" in msg:' in src
    assert "warnings.append(fetchers.base_debs_warning(gone))" in src
    # ...which is the words fetch_debs uses for a partial fetch.
    assert "could not fetch {" in (REPO / "domovoi" / "satellite_media" / "fetchers.py").read_text(encoding="utf-8")


# ─── stage 2 ──────────────────────────────────────────────────────────────


def _online_debs() -> str:
    body = overlay.render_stage2("domovoi")
    start = body.index('if [ ! -f "$STEPS/online-debs" ] && [ -f /var/lib/domovoi/internet-off ]; then')
    end = body.index("# 3. The client's Python dependencies.")
    return "\n".join(ln for ln in body[start:end].splitlines() if not ln.lstrip().startswith("#"))


def test_stage_2_repairs_only_with_fresh_lists_and_never_removes():
    code = _online_debs()
    update = code.index("apt-get update")
    gate = code.index('if [ "$UPDATE_OK" = 1 ]; then')
    fix = code.index("-f install")
    assert update < gate < fix, "the repair runs only after an update that worked"
    update_line = next(ln for ln in code.splitlines() if "apt-get update" in ln)
    assert "--error-on=any" in update_line, "a failed fetch must fail the update (stale lists)"
    for line in code.splitlines():
        if "apt-get" in line and ("install" in line) and "-f install" in line:
            assert "--no-remove" in line
    installs = [ln for ln in code.splitlines() if re.search(r"apt-get .*install -y", ln)]
    assert installs and all("--no-remove" in ln for ln in installs)
    assert "for TRY in 1 2 3" in code, "the lists are retried, not given up on at once"


def test_stage_2_says_the_boot_step_retries():
    code = _online_debs()
    assert "the boot step installs what is missing at every boot" in code
    assert "retrying next boot" not in code, "stage 2 is marked done; it does not run again"


# ─── one list ─────────────────────────────────────────────────────────────


def test_the_satellite_s_base_list_is_the_build_s():
    from satellite import base_packages

    assert tuple(b for b, _ in base_packages.BASE_PACKAGES) == fetchers.BASE_APT_PACKAGES
    names = dict(base_packages.BASE_PACKAGES)
    for pkg, alternates in fetchers.DEB_ALTERNATES.items():
        assert set(alternates) <= set(names[pkg]), pkg


# ─── internet access turned off at card prep ──────────────────────────────


def test_a_card_prepared_with_the_internet_off_carries_root_s_flag():
    on = overlay.render_firstrun("domovoi", "xvf3800_usb", "voice", "portal")
    off = overlay.render_firstrun("domovoi", "xvf3800_usb", "voice", "portal", internet_off=True)
    assert 'INTERNET_ACCESS=""' in on and 'INTERNET_ACCESS="never"' in off
    block = off[off.index('if [ "$INTERNET_ACCESS" = "never" ]; then'):]
    block = block[:block.index("\nfi\n")]
    assert ">/var/lib/domovoi/internet-off" in block and "rm -f /var/lib/domovoi/internet-off" in block
    # Written before any step can fail, right after the state directory exists.
    assert off.index('mkdir -p "$STEPS"') < off.index("internet-off") < off.index("# 1.")
    from satellite import base_packages

    assert str(base_packages.INTERNET_OFF_FLAG).replace("\\", "/") == "/var/lib/domovoi/internet-off"


def test_the_builder_passes_the_server_s_answer():
    src = (REPO / "domovoi" / "satellite_media" / "builder.py").read_text(encoding="utf-8")
    assert "internet_off=egress.internet_turned_off()," in src


# ─── stage 2's package step, run (internet-off flag) ──────────────────────
#
# Integration 1017d: stage 2 still ran `apt-get update` (and the online
# repair) on a card carrying root's internet-off flag. The rendered block
# runs here under bash (Git Bash on Windows, never System32's WSL launcher)
# with apt-get replaced by a function that only logs: no network, no root.


def _find_bash() -> str | None:
    import os
    import shutil
    import sys

    if sys.platform != "win32":
        return shutil.which("bash")
    candidates = []
    git = shutil.which("git")
    if git:
        for up in (Path(git).parent.parent, Path(git).parent.parent.parent):
            candidates.append(up / "bin" / "bash.exe")
    candidates.append(Path(os.environ.get("ProgramFiles", r"C:\Program Files")) / "Git" / "bin" / "bash.exe")
    for c in candidates:
        if c.is_file() and "system32" not in str(c).lower():
            return str(c)
    return None


_BASH = _find_bash()

# Shell functions shadow the real commands (a PATH entry would need a POSIX
# path, which a Windows temp folder is not under Git Bash).
_FAKES = (
    'apt-get() { echo "apt-get $*" >>"$FAKE_LOG"; return 0; }\n'
    "sleep() { :; }\n"
)


# Real apt with the network down: `apt-get update` warns "Failed to fetch",
# keeps the stale lists and exits 0, unless --error-on=any is given (then
# it exits 100). Integration 1020a: stage 2 took that 0 as fresh lists and
# ran the repair (`-f install`) on stale ones.
_FAKES_NETWORK_DOWN = (
    'apt-get() {\n'
    '  echo "apt-get $*" >>"$FAKE_LOG"\n'
    '  case " $* " in\n'
    '    *" update "*)\n'
    '      echo "W: Failed to fetch http://deb.debian.org/debian/dists/trixie/InRelease" >&2\n'
    '      case " $* " in *" --error-on=any "*) return 100;; esac\n'
    '      return 0;;\n'
    '  esac\n'
    '  return 0\n'
    '}\n'
    "sleep() { :; }\n"
)


def _run_package_step(tmp_path: Path, *, flag: bool, fakes: str = _FAKES) -> tuple[str, list[str], bool]:
    import subprocess

    body = overlay.render_stage2("domovoi")
    block = body[body.index("# 2. System packages."):body.index("# 3. The client's Python dependencies.")]
    flag_path = tmp_path / "internet-off"
    if flag:
        flag_path.write_text("off\n")
    assert block.count("/var/lib/domovoi/internet-off") == 2  # the comment and the test
    block = block.replace("/var/lib/domovoi/internet-off", flag_path.as_posix())
    steps = tmp_path / "steps"
    steps.mkdir()
    log = tmp_path / "apt.log"
    script = tmp_path / "step2.sh"
    script.write_bytes((
        "set -u\n"
        + fakes
        + f'STEPS="{steps.as_posix()}"\n'
        f'export FAKE_LOG="{log.as_posix()}"\n'
        'APT_PACKAGES="mpg123 alsa-utils"\n'
        + block
    ).encode())
    out = subprocess.run([_BASH, script.as_posix()], capture_output=True, text=True, timeout=60)
    assert out.returncode == 0, out.stderr
    calls = log.read_text().splitlines() if log.exists() else []
    return out.stdout, calls, (steps / "online-debs").exists()


@pytest.mark.skipif(_BASH is None, reason="no usable bash (Git Bash on Windows)")
def test_stage_2_stays_offline_on_a_card_with_the_internet_off_flag(tmp_path):
    stdout, calls, marked = _run_package_step(tmp_path, flag=True)
    assert calls == [], f"stage 2 ran apt with internet access turned off: {calls}"
    assert "internet access is turned off on this card" in stdout
    assert "the boot step installs what is missing" in stdout
    # Not marked done: nothing was installed here.
    assert not marked


@pytest.mark.skipif(_BASH is None, reason="no usable bash (Git Bash on Windows)")
def test_stage_2_still_goes_online_without_the_flag(tmp_path):
    stdout, calls, marked = _run_package_step(tmp_path, flag=False)
    assert calls and calls[0].startswith("apt-get update"), calls
    assert any("-f install -y --no-remove" in c for c in calls)
    assert any(c.endswith("install -y --no-install-recommends --no-remove mpg123") for c in calls)
    assert "system packages present" in stdout and marked


@pytest.mark.skipif(_BASH is None, reason="no usable bash (Git Bash on Windows)")
def test_stage_2_does_not_repair_on_stale_lists_with_the_network_down(tmp_path):
    stdout, calls, marked = _run_package_step(tmp_path, flag=False, fakes=_FAKES_NETWORK_DOWN)
    updates = [c for c in calls if c.startswith("apt-get update")]
    assert len(updates) == 3, calls
    assert all("--error-on=any" in c for c in updates), updates
    assert calls == updates, f"stage 2 installed or repaired on stale lists: {calls}"
    assert "could not fetch every package list (try 3 of 3)" in stdout
    assert "no fresh package lists (no network?)" in stdout
    assert "the boot step installs what is missing" in stdout
    # The step ends normally (the boot carries on) and is not marked done.
    assert not marked
