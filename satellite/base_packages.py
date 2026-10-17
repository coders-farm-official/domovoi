"""The satellite's base system packages: which are missing, and — as root,
at boot — putting them back.

Found on the office satellite (2026-10-17): every wake word logged "could
not run mpg123 ([Errno 2] No such file or directory)". mpg123 is in the base
set every card carries (domovoi/satellite_media/fetchers.py
BASE_APT_PACKAGES), and it still was not there, because of how the two
setup stages hand it on:

1. Stage 1 installs the card's deb cache with a plain ``dpkg -i``, which
   does not resolve dependencies, and the cache holds only the packages
   named (``apt-get download``). Debian's mpg123 depends on nine libraries
   Pi OS Lite does not have (libmpg123-0t64, libout123-0t64, libsyn123-0t64,
   libaudio2, libopenal1, libpulse0, ...), so it is left unpacked and
   unconfigured — and its ``/usr/bin/mpg123`` is an alternatives link that
   only configuring creates. Nothing to run, whatever the cache held.
2. Stage 2 is the step that fixes that, online. But it ran
   ``apt-get -f install -y`` whether or not ``apt-get update`` had worked,
   and with the Wi-Fi down at that moment (the office unit's first boots
   show the link down for 16 minutes) apt's repair for an unconfigured
   package whose dependencies it cannot fetch is to REMOVE it. The install
   that followed failed, stage 2 said "retrying next boot" — and then
   marked itself done because the satellite service had started, so it
   never ran again.

Reproduced in a Debian trixie container (2026-10-17): the cached
mpg123 alone, ``dpkg -i`` → "install ok unpacked", no /usr/bin/mpg123; then
``apt-get update`` with no network and ``apt-get -f install -y`` → "The
following packages will be REMOVED: mpg123".

So a satellite now heals itself: the boot-time step (root,
``domovoi-provisioning.service`` → ``provisioning_mode.main``, the same
step that applies the network settings, satellite/net_settings.py) runs
:func:`repair` on every boot once stage 2 is done. It looks up which base
packages are missing (``dpkg-query``, read-only), and only when some are:
waits for a default route (up to :data:`NETWORK_WAIT_SEC`), refreshes the
package lists, and installs exactly those names with
``--no-install-recommends --no-remove`` — apt may add what they depend on,
and may never take anything away. It records what it found and did in
``/var/lib/domovoi/base-packages.json`` (root's directory, 0644), which the
client reads, logs into the room's retained log, and turns into the
dashboard's "reboot needed" while anything is still missing.

What root runs here is fixed in this file: the package names are the
constants below, never read from the satellite account's files or the
server. Stdlib only, like everything provisioning mode imports.
"""

from __future__ import annotations

import json
import logging
import os
import subprocess
import sys
import time
from pathlib import Path
from typing import Any, Callable

log = logging.getLogger("base-packages")

#: Bumped when this module starts checking something new.
BASE_PACKAGES_VERSION = 1

#: The base apt set, as a satellite needs it at run time: mirrors
#: domovoi/satellite_media/fetchers.py BASE_APT_PACKAGES (a core test holds
#: the two to the same names), with the names a package goes by on the
#: releases a card runs. A package counts as present when ANY of its names
#: is installed; the FIRST name apt has a candidate for is the one
#: installed. (Debian's 64-bit time_t transition renamed libasound2; on
#: trixie the old name is virtual and apt resolves it on its own.)
BASE_PACKAGES: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("mpg123", ("mpg123",)),
    ("libportaudio2", ("libportaudio2", "libportaudio2t64")),
    ("libjack-jackd2-0", ("libjack-jackd2-0", "libjack0")),
    ("libasound2", ("libasound2t64", "libasound2")),
    ("alsa-utils", ("alsa-utils",)),
    ("mtools", ("mtools",)),
    ("dosfstools", ("dosfstools",)),
    ("libusb-1.0-0", ("libusb-1.0-0",)),
    ("dnsmasq-base", ("dnsmasq-base",)),
    ("avahi-daemon", ("avahi-daemon",)),
    ("libnss-mdns", ("libnss-mdns",)),
)

#: Root's own state directory (stage 1 made it).
STATE_DIR = Path("/var/lib/domovoi")
REPORT_PATH = STATE_DIR / "base-packages.json"
#: Stage 2's done flag: until it exists, stage 2 owns the packages (two apt
#: runs at once would fight over the dpkg lock).
STAGE2_DONE = STATE_DIR / "bootstrap-done"

#: How long the boot step waits for the network before giving up for this
#: boot (it runs again at the next one), and how often it looks.
NETWORK_WAIT_SEC = 300.0
NETWORK_POLL_SEC = 10.0
UPDATE_TIMEOUT_SEC = 300.0
INSTALL_TIMEOUT_SEC = 900.0
#: dpkg's lock may be held by stage 2 or unattended-upgrades: wait for it.
_APT_OPTS = ("-o", "DPkg::Lock::Timeout=120", "-o", "Acquire::Retries=3")

Runner = Callable[..., Any]


def _run(run: Runner, argv: list[str], timeout: float) -> tuple[int, str]:
    """``(returncode, last output)``; 127 when the program is missing, 124
    on a timeout. Never raises."""
    env = dict(os.environ, DEBIAN_FRONTEND="noninteractive", LC_ALL="C")
    try:
        r = run(argv, capture_output=True, text=True, timeout=timeout, env=env,
                stdin=subprocess.DEVNULL)
    except FileNotFoundError as e:
        return 127, str(e)
    except subprocess.TimeoutExpired:
        return 124, f"{argv[0]} timed out after {timeout:.0f} s"
    except (OSError, subprocess.SubprocessError) as e:
        return 1, str(e)
    out = ((getattr(r, "stdout", "") or "") + (getattr(r, "stderr", "") or "")).strip()
    return int(getattr(r, "returncode", 1)), out[-400:]


def is_installed(name: str, run: Runner = subprocess.run) -> bool:
    """dpkg says ``install ok installed`` (unpacked-but-unconfigured is not:
    that is exactly the state stage 1 can leave a package in)."""
    rc, out = _run(run, ["dpkg-query", "-W", "-f=${Status}", name], 30.0)
    return rc == 0 and out.strip().endswith("install ok installed")


def missing(run: Runner = subprocess.run) -> list[str]:
    """The base packages none of whose names is installed, by base name."""
    return [base for base, names in BASE_PACKAGES
            if not any(is_installed(n, run) for n in names)]


def _has_candidate(name: str, run: Runner) -> bool:
    rc, out = _run(run, ["apt-cache", "policy", name], 60.0)
    if rc != 0:
        return False
    for line in out.splitlines():
        line = line.strip()
        if line.startswith("Candidate:"):
            value = line.split(":", 1)[1].strip()
            return bool(value) and value != "(none)"
    return False


def install_name(base: str, run: Runner = subprocess.run) -> str:
    """The name to ask apt for: the first one it has a candidate for, else
    the first name (apt then says why it cannot)."""
    names = dict(BASE_PACKAGES).get(base, (base,))
    for n in names:
        if _has_candidate(n, run):
            return n
    return names[0]


def default_route(run: Runner = subprocess.run) -> bool:
    rc, out = _run(run, ["ip", "route", "show", "default"], 10.0)
    return rc == 0 and out.startswith("default")


def _boot_id() -> str | None:
    try:
        return Path("/proc/sys/kernel/random/boot_id").read_text(encoding="ascii").strip()[:64]
    except OSError:
        return None


def write_report(doc: dict[str, Any], path: Path | None = None) -> bool:
    """Atomic, 0644, in root's own directory; never follows a link at the
    target (a planted one is replaced, not written through)."""
    path = path or REPORT_PATH
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_name(path.name + ".tmp")
        try:
            tmp.unlink()
        except FileNotFoundError:
            pass
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0), 0o644)
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as f:
            json.dump(doc, f, indent=2, sort_keys=True)
        os.replace(tmp, path)
        return True
    except OSError as e:
        log.warning("base packages: could not write %s: %s", path, e)
        return False


def read_report(path: Path | None = None) -> dict[str, Any] | None:
    try:
        raw = (path or REPORT_PATH).read_bytes()
    except OSError:
        return None
    if len(raw) > 16_384:
        return None
    try:
        doc = json.loads(raw)
    except ValueError:
        return None
    return doc if isinstance(doc, dict) else None


def repair(
    *,
    run: Runner = subprocess.run,
    sleep: Callable[[float], None] = time.sleep,
    clock: Callable[[], float] = time.monotonic,
    stage2_done: Path | None = None,
    report_path: Path | None = None,
    network_wait: float = NETWORK_WAIT_SEC,
    wait_for_stage2: bool = True,
) -> dict[str, Any]:
    """Find the missing base packages and install them. Root only (the
    caller checks). Returns, and records, ``{state, missing, installed,
    still_missing, errors, ...}`` where state is ``ok`` (nothing missing),
    ``repaired``, ``failed``, ``no_network`` or ``deferred`` (stage 2 has
    not finished; it owns the packages until then)."""
    doc: dict[str, Any] = {
        "version": BASE_PACKAGES_VERSION, "ts": time.time(), "boot_id": _boot_id(),
        "missing": [], "installed": [], "still_missing": [], "errors": [],
    }
    done = stage2_done if stage2_done is not None else STAGE2_DONE
    if wait_for_stage2 and not done.exists():
        doc["state"] = "deferred"
        doc["detail"] = "stage 2 has not finished; it installs the base packages"
        write_report(doc, report_path)
        return doc
    gone = missing(run)
    doc["missing"] = gone
    if not gone:
        doc["state"] = "ok"
        write_report(doc, report_path)
        log.info("base packages: all %d present", len(BASE_PACKAGES))
        return doc
    log.warning("base packages missing: %s; installing them online", ", ".join(gone))
    deadline = clock() + network_wait
    while not default_route(run):
        if clock() >= deadline:
            doc["state"] = "no_network"
            doc["still_missing"] = gone
            doc["detail"] = (f"no network within {network_wait:.0f} s of boot; "
                             "the next boot tries again")
            write_report(doc, report_path)
            log.warning("base packages: %s", doc["detail"])
            return doc
        sleep(NETWORK_POLL_SEC)
    rc, out = _run(run, ["apt-get", *_APT_OPTS, "update", "-qq"], UPDATE_TIMEOUT_SEC)
    if rc != 0:
        doc["errors"].append(f"apt-get update: rc={rc}: {out[-200:]}")
    for base in gone:
        name = install_name(base, run)
        rc, out = _run(run, ["apt-get", *_APT_OPTS, "install", "-y", "--no-install-recommends",
                             "--no-remove", name], INSTALL_TIMEOUT_SEC)
        if rc == 0:
            doc["installed"].append(name)
        else:
            doc["errors"].append(f"{name}: rc={rc}: {out[-200:]}")
    doc["still_missing"] = missing(run)
    doc["state"] = "failed" if doc["still_missing"] else "repaired"
    write_report(doc, report_path)
    if doc["still_missing"]:
        log.warning("base packages: still missing after the install: %s (%s)",
                    ", ".join(doc["still_missing"]), "; ".join(doc["errors"])[:300])
    else:
        log.info("base packages: installed %s", ", ".join(doc["installed"]))
    return doc


# ─── the client's reading of root's record ────────────────────────────────


def reboot_note(present_missing: list[str], report: dict[str, Any] | None,
                boot_id: str | None) -> str | None:
    """What the dashboard's "reboot needed" says while base packages are
    missing (None when none are). A reboot is what runs :func:`repair`; the
    note says what the last run on this boot found, when there was one."""
    if not present_missing:
        return None
    names = ", ".join(present_missing)
    last = ""
    if isinstance(report, dict) and report.get("boot_id") == boot_id:
        state = report.get("state")
        if state == "no_network":
            last = "; this boot's try found no network"
        elif state == "failed":
            last = "; this boot's install failed (see the log)"
        elif state == "deferred":
            last = "; setup has not finished yet"
    return (f"system packages missing ({names}): reboot this satellite with the network "
            f"up and it installs them{last}")[:200]


def main(argv: list[str] | None = None) -> int:
    """``sudo ~/satellite-venv/bin/python -m satellite.base_packages``: one
    run by hand (a hand-built unit, or a repair without a reboot)."""
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")
    if sys.platform != "linux":
        log.error("base packages are only repaired on the satellite itself")
        return 2
    geteuid = getattr(os, "geteuid", None)
    if geteuid is None or geteuid() != 0:
        log.error("run as root (sudo); listing only: missing = %s", missing() or "none")
        return 2
    doc = repair(wait_for_stage2=False)  # by hand: whatever stage 2 did
    return 0 if doc["state"] in ("ok", "repaired") else 1


if __name__ == "__main__":
    sys.exit(main())
