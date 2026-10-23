"""Keep the house network's profile in step with the router's security.

The setup page builds ``domovoi-home`` for the router as it was that day.
Routers change: Kamron switched his access point to "WPA3-Personal +
WPA2-PSK" to match his main router (2026-10-22), and every satellite set up
before it dropped off and stayed off — a power cycle did not help — because
the saved profile (``wpa-psk``, PMF at NetworkManager's default) lets NM
offer WPA3 (SAE) to a mixed router, and the Pi Zero 2 W's radio cannot
finish SAE (see ``provisioning_mode._PMF_DISABLE``).

This module decides, FROM A SCAN and never blindly, whether the profile's
security must change:

* mixed (WPA2+WPA3) and the profile is ``wpa-psk`` with PMF not off:
  ``wpa-psk`` + PMF off;
* WPA3-only: ``sae`` + PMF required on a radio that can do SAE; on one that
  cannot (Pi Zero 2 W / Zero W / 3 B) a plain reason instead, which the
  client shows and backs off on rather than rebooting;
* WPA2-only after a failed join with PMF off: ``wpa-psk`` + PMF default (a
  router that now REQUIRES PMF);
* WPA2 again after WPA3-only (the profile is ``sae``): back to ``wpa-psk``.

It runs in two places, both as root:

* at boot, from ``satellite/net_settings.py`` (:func:`adapt`), before the
  first join can give up — what would have made the power cycle work;
* at runtime, through the argv-less helper
  ``satellite/scripts/domovoi-wifi-adapt`` (the same table, in sh, because
  the client is not root and the sudoers line admits a root-owned file
  only), when the client's recovery could not bring the link back.

:func:`decide` is the table; ``test_wifi_security_matrix.py`` checks the sh
helper against it case by case.
"""

from __future__ import annotations

import re
import subprocess
import time
from dataclasses import dataclass, field
from typing import Callable

from satellite import wifi_reasons

HOME_PROFILE = "domovoi-home"

PSK, SAE = "wpa-psk", "sae"
PMF_DEFAULT, PMF_DISABLE, PMF_OPTIONAL, PMF_REQUIRED = (
    "default", "disable", "optional", "required")
_PMF_BY_NUMBER = {"0": PMF_DEFAULT, "1": PMF_DISABLE, "2": PMF_OPTIONAL, "3": PMF_REQUIRED}

# What a mismatch no profile change can fix is called (the sh helper prints
# the same words as `kind=`).
WPA3_ONLY_BOARD = "wpa3-only-board"
ENTERPRISE_NOW = "enterprise-now"
WEP_NOW = "wep-now"
OPEN_NOW = "open-now"
PASSWORD_NOW = "password-now"

#: Plain words for each, for the log the server keeps, the health record
#: and the dashboard. "This speaker", because that is what the household
#: is looking at.
MISMATCH_WORDS = {
    WPA3_ONLY_BOARD: "the router switched to WPA3-only; this speaker needs WPA2/WPA3 "
                     "mixed mode (often called \"WPA2-PSK/WPA3-SAE\" or "
                     "\"WPA3-Personal Transition\") - turn it on in the router",
    ENTERPRISE_NOW: "the router now asks for a username (802.1X / Enterprise), which "
                    "this speaker can't use - give it a network with just a password",
    WEP_NOW: "the router switched to WEP, which this speaker can't use - set it to "
             "WPA2 or WPA2/WPA3 mixed",
    OPEN_NOW: "the router no longer has a Wi-Fi password - set the speaker up again "
              "(hold its button for the setup network) or turn the password back on",
    PASSWORD_NOW: "the router now needs a Wi-Fi password - set the speaker up again",
}

#: How often a satellite stuck on a mismatch rescans (and retries), so it
#: comes back on its own once the router is changed back.
MISMATCH_RETRY_SEC = 5 * 60.0


_RESULT = re.compile(r"^result=(\S+) kind=(\S+)\s*$")


def parse_result(line: str) -> tuple[str | None, str | None]:
    """``(state, kind)`` from the helper's last line ``result=<s> kind=<k>``
    (``-`` for no kind); ``(None, None)`` for anything else."""
    m = _RESULT.match((line or "").strip())
    if not m:
        return None, None
    kind = m.group(2)
    return m.group(1), (None if kind == "-" else kind)


def normalize_pmf(value: str | None) -> str:
    """NetworkManager prints PMF as ``default``, ``1 (disable)``, ``3`` ...
    depending on the nmcli version and mode; one word out."""
    v = (value or "").strip().lower()
    m = re.match(r"(\d)", v)
    if m:
        return _PMF_BY_NUMBER.get(m.group(1), PMF_DEFAULT)
    for word in (PMF_DEFAULT, PMF_DISABLE, PMF_OPTIONAL, PMF_REQUIRED):
        if word in v:
            return word
    return PMF_DEFAULT


def board_can_sae(model: str | None) -> bool | None:
    """Whether this board's radio can complete WPA3 (SAE). False for the
    Broadcom chips that cannot (Pi Zero 2 W and Zero W, Pi 3 B: brcmfmac,
    raspberrypi/linux#4976); None for a board we know nothing about, which
    is then allowed to try."""
    low = (model or "").lower()
    if not low:
        return None
    if "zero 2 w" in low or "zero w" in low:
        return False
    if low.startswith("raspberry pi 3 model b") and "plus" not in low:
        return False
    if low.startswith("raspberry pi"):
        return True
    return None


def _tokens(security: str | None) -> set[str]:
    return set((security or "").upper().replace(",", " ").split()) - {"--"}


@dataclass(frozen=True)
class Plan:
    """``state``: ``ok`` (nothing to change), ``change`` (try ``attempts``
    in order), ``mismatch`` (nothing to try: ``kind`` says why) or
    ``not-visible`` (the router is not in the scan: nothing to decide)."""

    state: str
    attempts: tuple[tuple[str, str], ...] = ()
    kind: str | None = None
    why: str = ""


def decide(security: str | None, key_mgmt: str | None, pmf: str | None,
           can_sae: bool | None, *, after_failure: bool) -> Plan:
    """The table. ``security`` is the scan's SECURITY for the home network
    (None: not seen); ``key_mgmt``/``pmf`` the saved profile's;
    ``after_failure``: a join with the current settings has just failed
    (the runtime helper), rather than a boot-time look before any join."""
    if security is None:
        return Plan("not-visible", why="the house network is not in the scan")
    t = _tokens(security)
    km = (key_mgmt or "").strip().lower()
    pmf = normalize_pmf(pmf)
    if ("802.1X" in t or "EAP" in t) and not t & {"PSK", "WPA-PSK", "SAE"}:
        return Plan("mismatch", kind=ENTERPRISE_NOW, why="the router is 802.1X")
    if "WEP" in t:
        return Plan("mismatch", kind=WEP_NOW, why="the router is WEP")
    if not t:
        if km in (PSK, SAE):
            return Plan("mismatch", kind=OPEN_NOW, why="the router is open")
        return Plan("ok", why="open, as the profile is")
    wpa = bool(t & {"WPA", "WPA1", "WPA2", "PSK", "WPA-PSK"})
    sae = bool(t & {"WPA3", "SAE"})
    if km not in (PSK, SAE):
        return Plan("mismatch", kind=PASSWORD_NOW, why="the router has a password now")
    if wpa:
        mixed = sae
        if km == SAE:
            if mixed and can_sae is not False and not after_failure:
                return Plan("ok", why="WPA3 profile on a mixed router, on a radio that can")
            tries = [(PSK, PMF_DISABLE)] + ([] if mixed else [(PSK, PMF_DEFAULT)])
            return Plan("change", tuple(tries),
                        why="the router offers WPA2 again; back to wpa-psk")
        if mixed and pmf != PMF_DISABLE:
            return Plan("change", ((PSK, PMF_DISABLE),),
                        why="the router is WPA2/WPA3 mixed; PMF off keeps the join on WPA2")
        if not mixed and after_failure and pmf == PMF_DISABLE:
            return Plan("change", ((PSK, PMF_DEFAULT),),
                        why="WPA2 with PMF off failed; the router may require PMF")
        return Plan("ok", why="the profile matches the router")
    # WPA3-only.
    if km == SAE:
        if pmf != PMF_REQUIRED:
            return Plan("change", ((SAE, PMF_REQUIRED),), why="WPA3 needs PMF required")
        return Plan("ok", why="WPA3 profile on a WPA3 router")
    if can_sae is False:
        return Plan("mismatch", kind=WPA3_ONLY_BOARD, why="WPA3-only on a radio that can't do SAE")
    return Plan("change", ((SAE, PMF_REQUIRED),), why="the router is WPA3-only now")


# ─── Applying it (root, at boot) ─────────────────────────────────────────


def _split_terse(line: str) -> list[str]:
    fields, cur, i = [], [], 0
    while i < len(line):
        ch = line[i]
        if ch == "\\" and i + 1 < len(line):
            cur.append(line[i + 1])
            i += 2
            continue
        if ch == ":":
            fields.append("".join(cur))
            cur = []
        else:
            cur.append(ch)
        i += 1
    fields.append("".join(cur))
    return fields


def _out(r) -> str:
    out = r.stdout
    if isinstance(out, bytes):
        out = out.decode("utf-8", "replace")
    return out or ""


def _get(run, nmcli: str, field_: str) -> str | None:
    try:
        r = run([nmcli, "-t", "-g", field_, "connection", "show", "id", HOME_PROFILE],
                capture_output=True, text=True, timeout=20)
    except (OSError, subprocess.TimeoutExpired):
        return None
    if r.returncode != 0:
        return None
    lines = _out(r).splitlines()
    return _split_terse(lines[0])[0] if lines else ""


def scan_security(run, nmcli: str, ssid: str) -> str | None:
    """The SECURITY of every scan row carrying ``ssid`` (None: not seen)."""
    try:
        r = run([nmcli, "-t", "-f", "SSID,SECURITY", "device", "wifi", "list",
                 "--rescan", "yes"], capture_output=True, text=True, timeout=30)
    except (OSError, subprocess.TimeoutExpired):
        return None
    if r.returncode != 0:
        return None
    seen, flags = False, []
    for ln in _out(r).splitlines():
        row = _split_terse(ln.strip())
        if row and row[0] == ssid:
            seen = True
            if len(row) > 1 and row[1].strip() not in ("", "--"):
                flags.append(row[1].strip())
    return " ".join(flags) if seen else None


@dataclass
class Result:
    state: str               # connected | ok | adapted | failed | mismatch | not-visible | no-profile | error
    kind: str | None = None
    words: str = ""
    changed: list[str] = field(default_factory=list)

    def document(self) -> dict[str, object]:
        return {"state": self.state, "kind": self.kind, "words": self.words,
                "changed": list(self.changed)}


def adapt(run, nmcli: str, *, model: str | None, after_failure: bool = False,
          connect: bool = True, scans: int = 3,
          sleep: Callable[[float], None] = time.sleep) -> Result:
    """Look at the router, and change ``domovoi-home``'s security when the
    table says so; then bring it up (``connect``). Never touches a profile
    that is up: a working link is never dropped for a theory."""
    ssid = _get(run, nmcli, "802-11-wireless.ssid")
    if ssid is None:
        return Result("no-profile", words=f"no {HOME_PROFILE} profile")
    if (_get(run, nmcli, "GENERAL.STATE") or "").strip() == "activated":
        return Result("connected", words="the house network is up")
    km = _get(run, nmcli, "802-11-wireless-security.key-mgmt")
    pmf = _get(run, nmcli, "802-11-wireless-security.pmf")
    security = None
    for i in range(max(1, scans)):
        security = scan_security(run, nmcli, ssid)
        if security is not None:
            break
        if i + 1 < scans:
            sleep(5.0)
    plan = decide(security, km, pmf, board_can_sae(model), after_failure=after_failure)
    if plan.state == "not-visible":
        return Result("not-visible", words=plan.why)
    if plan.state == "ok":
        return Result("ok", words=plan.why)
    if plan.state == "mismatch":
        return Result("mismatch", kind=plan.kind, words=MISMATCH_WORDS[plan.kind or ""])
    res = Result("failed", words=plan.why)
    for new_km, new_pmf in plan.attempts:
        try:
            m = run([nmcli, "connection", "modify", "id", HOME_PROFILE,
                     "802-11-wireless-security.key-mgmt", new_km,
                     "802-11-wireless-security.pmf", new_pmf],
                    capture_output=True, text=True, timeout=30)
        except (OSError, subprocess.TimeoutExpired) as e:
            return Result("error", words=f"could not change {HOME_PROFILE}: {type(e).__name__}",
                          changed=res.changed)
        if m.returncode != 0:
            err = m.stderr.decode("utf-8", "replace") if isinstance(m.stderr, bytes) else (m.stderr or "")
            return Result("error", words=f"could not change {HOME_PROFILE}: "
                          f"{wifi_reasons.reason_line(err + chr(10) + _out(m))}",
                          changed=res.changed)
        res.changed.append(f"{HOME_PROFILE}: {km or '?'}/{normalize_pmf(pmf)} -> "
                           f"{new_km}/{new_pmf} ({plan.why})")
        km, pmf = new_km, new_pmf
        if not connect:
            res.state = "adapted"
            return res
        try:
            up = run([nmcli, "--wait", "45", "connection", "up", "id", HOME_PROFILE],
                     capture_output=True, text=True, timeout=60)
        except (OSError, subprocess.TimeoutExpired):
            continue
        if up.returncode == 0:
            res.state = "adapted"
            return res
    return res
